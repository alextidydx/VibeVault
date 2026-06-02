import React from 'react';
import classNames from "classnames";
import axios from 'axios';

import '../../../styles/ui/at-work.scss'
import { BASE } from '../../utils/basePath.js';

export default class AtWork extends React.Component {
	container = React.createRef()
	state = {
		showOverlay : false,
		desc : "",
		rating : 0.0,
		modified : false,
		deleted : false,
		error : false
	};

	componentDidMount() {
		this.setState({ 
			desc: this.props.data.desc, 
			rating: this.props.data.rating,
			modified: this.props.data.custom_modified
		 });
	}

	toggleOverlay = (e) => {

		this.setState({
			showOverlay: !this.state.showOverlay
		});
		return false;
	}

	hideOverlay = (e) => {
		this.setState({
			showOverlay: false
		});
		return false;
	}

	delete = () => {
		axios({
			method: 'get',
			url: BASE +  'api/delete/' +  this.props.data.id
		}).then((response) => {
			this.setState({ deleted : true });
		}).catch((error) => {
			console.error("Delete failed", error);
			this.setState({ error : true  });
		});
	}

	editDesc = (newDesc) => {

		axios({
			method: 'get',
			url: BASE + 'api/set-desc/' +  this.props.data.id,
			params: {
				desc: newDesc
			}
		}).then((response) => {
			this.setState({ desc: response.data.desc, modified : true });
		}).catch((error) => {
			console.error("Description update failed", error);
			this.setState({ showOverlay : false, desc: "Something went wrong!", error : true  });
		});
	}

	edit = () => {
		this.hideOverlay();
		if (this.props.showDescInput) {
			this.props.showDescInput(this);
		}
	}

	getDesc = () => {
		var desc = this.props.data.desc;
		if (this.state.desc !== "") {
			desc = this.state.desc;
		}
		return desc;
	}

	updateRating = (rating) => {
		this.setState({ rating: rating + 1 });
		axios({
			method: 'get',
			url: BASE +  'api/set-rating/' +  this.props.data.id,
			params: {
				rating: rating + 1
			}
		}).then((response) => {
			this.setState({ rating : response.data.rating, modified : true });
		}).catch((error) => {
			console.error("Rating update failed", error);
			this.setState({ error : true });
		});
	}
	
	render() {
		const classnames = classNames({
			"e__results-image" : true,
			"e__results-image--overlay" : this.state.showOverlay,
			"e__results-image--custom" : this.state.modified,
			"e__results-image--deleted" : this.state.deleted,
			"e__results-image--error" : this.state.error
		})
		const stars = Array.from({ length: 10 }, (_, i) => i);
		let rating = this.state.rating;
		if (rating < 0) rating = "No rating"
		let filename = this.props.data.filename.replace(/^.*[\\/]/, '')
		return (
			<div className={classnames} ref={this.container} key={this.props.data.id}>
				{
					(this.props.data.content_type === "video" ) ?
						<video loop controls className="e__at-work__video">
							<source src={BASE + "api/img/" + this.props.data.id} type="video/mp4" />
						</video>
					: 
						<a href={BASE + "api/img/" + this.props.data.id} target="_blank"><img src={BASE + "api/img/" + this.props.data.id} alt={filename} className="e__at-work__image"/></a>
				}
				{
					(this.props.data.similarity) && <p className="e__at-work__similarity">{ Math.round(this.props.data.similarity*100) }</p>
				}		
				<div className="e__at-work__ocd">
					<div className="e__results-image__ocd-info">
						<p className="e__at-work__filename" title={this.props.data.filename}>{filename}</p>
						<p className="e__at-work__rating" title={rating} onClick={this.toggleOverlay}>{rating}</p>
					</div>
					<div onClick={this.toggleOverlay} className="e__results-image__overlay-btn"></div>
				</div>

				<div className="e__results-image__overlay" >
					<div className="e__results-image__buttons">
						<div className="e__results-image__buttons-cl">
							<div className="e__results-image__button edit" onClick={this.edit} ></div>
							<div className="e__results-image__button delete" onClick={() => { if (window.confirm("Are you sure you want to delete this image?")) {  this.delete(); } }} ></div>
						</div>
						<div className="e__results-image__stars" >
							{
								stars.map((item) => {
									return <div 
										key={item} 
										className={classNames({
											"e__results-image__star" : true,
											"e__results-image__star--active" : item < this.state.rating
										})}
										onClick={(e) => { this.updateRating(item) }}
									></div>
								})
							}
						</div>
					</div>
				</div>
				
			</div>
		)
	}
}

